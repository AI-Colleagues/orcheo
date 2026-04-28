import { Avatar, AvatarFallback, AvatarImage } from "@/design-system/ui/avatar";
import { Badge } from "@/design-system/ui/badge";
import { Button } from "@/design-system/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/design-system/ui/card";
import { Input } from "@/design-system/ui/input";
import { Label } from "@/design-system/ui/label";
import { Separator } from "@/design-system/ui/separator";

import type { ProfileUser } from "../types";

interface ProfileGeneralTabProps {
  user: ProfileUser;
}

export function ProfileGeneralTab({ user }: ProfileGeneralTabProps) {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Profile Information</CardTitle>
          <CardDescription>
            Update your account profile information and email address.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="flex items-center space-x-4">
            <Avatar className="h-20 w-20">
              <AvatarImage src={user.avatar} alt={user.name} />
              <AvatarFallback>{user.name.charAt(0)}</AvatarFallback>
            </Avatar>
            <div className="space-y-1">
              <h3 className="font-medium">{user.name}</h3>
              <div className="flex items-center space-x-2">
                <Badge variant="outline">{user.role}</Badge>
                {user.joinDate ? (
                  <span className="text-sm text-muted-foreground">
                    Member since {user.joinDate}
                  </span>
                ) : null}
              </div>
              <Button size="sm" variant="outline">
                Change Avatar
              </Button>
            </div>
          </div>
          <Separator />
          <form className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  defaultValue={user.name}
                  placeholder="Your name"
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  defaultValue={user.email}
                  placeholder="Your email"
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label htmlFor="bio">Bio</Label>
              <textarea
                id="bio"
                className="flex min-h-[80px] w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
                placeholder="Write a short bio about yourself"
              />
            </div>
          </form>
        </CardContent>
        <CardFooter>
          <Button>Save Changes</Button>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Preferences</CardTitle>
          <CardDescription>
            Manage your notification preferences and timezone settings.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="timezone">Timezone</Label>
            <select
              id="timezone"
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="UTC">UTC (Coordinated Universal Time)</option>
              <option value="America/New_York">
                Eastern Time (US & Canada)
              </option>
              <option value="America/Chicago">
                Central Time (US & Canada)
              </option>
              <option value="America/Denver">
                Mountain Time (US & Canada)
              </option>
              <option value="America/Los_Angeles">
                Pacific Time (US & Canada)
              </option>
              <option value="Europe/London">London (GMT/BST)</option>
              <option value="Europe/Paris">Paris, Berlin, Rome (CET)</option>
            </select>
          </div>
        </CardContent>
        <CardFooter>
          <Button>Save Preferences</Button>
        </CardFooter>
      </Card>
    </div>
  );
}
